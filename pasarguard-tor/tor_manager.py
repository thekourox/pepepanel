import os
import time
import signal
import threading
import logging
import requests
import stem.process
from stem.control import Controller
from stem import Signal
import shutil
import platform
import json
import re
import psutil
import random

from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich import box

# Setup File Logging ONLY (No console spam)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("manager_logs.txt", mode='w', encoding='utf-8')]
)
logger = logging.getLogger("TorManager")

TEST_URL = "https://cloudflare.com/cdn-cgi/trace"
SPEED_TEST_URL = "https://speed.cloudflare.com/__down?bytes=100000" # 100KB payload
TIMEOUT = 15
LATENCY_THRESHOLD = 8.0
SPEED_THRESHOLD_KBS = 5.0
MAX_INSTANCES = 190

def detect_hardware_tier():
    try:
        ram_gb = psutil.virtual_memory().total / (1024**3)
    except:
        ram_gb = 4.0
    if ram_gb < 6.0:
        return 'LOW'
    elif ram_gb < 12.0:
        return 'MID'
    return 'HIGH'

HARDWARE_TIER = detect_hardware_tier()

# Global state for the dashboard
dashboard_state = {
    'phase': 'discovery',
    'discovery_msg': 'Initializing...',
    'discovery_progress': 0,
    'instances': {}
}

class TorInstance:
    def __init__(self, country, socks_port, control_port, data_dir, tor_cmd, available_fingerprints):
        self.country = country
        self.socks_port = socks_port
        self.control_port = control_port
        self.data_dir = data_dir
        self.tor_cmd = tor_cmd
        self.available_fingerprints = available_fingerprints
        self.fingerprint_index = 0
        
        self.process = None
        self.active = False
        
        # Init dashboard state
        dashboard_state['instances'][self.country] = {
            'port': str(self.socks_port),
            'ip': '...',
            'ping': '...',
            'speed': '...',
            'status': '🟡 Bootstrapping...'
        }
        
    def start(self):
        logger.info(f"[{self.country}] Starting Tor instance on SOCKS {self.socks_port}, Control {self.control_port}...")
        
        if os.path.exists(self.data_dir):
            shutil.rmtree(self.data_dir, ignore_errors=True)
        os.makedirs(self.data_dir, exist_ok=True)
        
        # OPTIMIZATION: Copy cached consensus and microdescriptors from the discovery node
        # This prevents 15 instances from downloading the same 10MB consensus simultaneously!
        discovery_data_dir = os.path.join(os.getcwd(), "tor_data", "discovery")
        if os.path.exists(discovery_data_dir):
            for filename in os.listdir(discovery_data_dir):
                if filename.startswith("cached-"):
                    src = os.path.join(discovery_data_dir, filename)
                    dst = os.path.join(self.data_dir, filename)
                    try:
                        if os.path.isfile(src):
                            shutil.copy2(src, dst)
                    except Exception:
                        pass
        
        config = {
            'SocksPort': f'0.0.0.0:{self.socks_port}',
            'ControlPort': f'127.0.0.1:{self.control_port}',
            'CookieAuthentication': '1',
            'DataDirectory': self.data_dir.replace('\\', '/'),
            'StrictNodes': '1',
            'Log': 'NOTICE stdout',
            'GeoIPFile': os.path.join(os.getcwd(), 'data', 'geoip').replace('\\', '/'),
            'GeoIPv6File': os.path.join(os.getcwd(), 'data', 'geoip6').replace('\\', '/'),
            'ClientUseIPv6': '0',
            'ClientPreferIPv6ORPort': '0'
        }
        
        if HARDWARE_TIER == 'LOW':
            config['MaxMemInQueues'] = '15 MB'
            config['NumCPUs'] = '1'
            config['AvoidDiskWrites'] = '1'
        elif HARDWARE_TIER == 'MID':
            config['MaxMemInQueues'] = '40 MB'
            config['NumCPUs'] = '2'
        
        # Pick the absolute fastest node to start with
        if self.available_fingerprints:
            best_fingerprint = self.available_fingerprints[0]
            self.fingerprint_index = 1
            config['ExitNodes'] = f'${best_fingerprint}'
            logger.info(f"[{self.country}] Using optimal starting fingerprint: {best_fingerprint}")
        else:
            config['ExitNodes'] = f'{{{self.country}}}'
            
        # AUTO-BYPASS HETZNER BLOCKS: Route Tor through Surfshark or Cloudflare WARP
        try:
            import socket
            wp_ports = []
            for conn in psutil.net_connections(kind='tcp'):
                if conn.status == 'LISTEN' and conn.laddr.ip in ('127.0.0.1', '0.0.0.0', '::1') and 20000 <= conn.laddr.port < 21000:
                    wp_ports.append(conn.laddr.port)
            
            if wp_ports:
                proxy_port = random.choice(wp_ports)
                config['Socks5Proxy'] = f'127.0.0.1:{proxy_port}'
                logger.info(f"[{self.country}] Bypassing DataCenter Blocks: Routing Tor through local Surfshark proxy (Port {proxy_port})")
            else:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    if s.connect_ex(('127.0.0.1', 40000)) == 0:
                        config['Socks5Proxy'] = '127.0.0.1:40000'
                        logger.info(f"[{self.country}] Bypassing DataCenter Blocks: Routing Tor through Cloudflare WARP (Port 40000)")
        except Exception as e:
            pass

        def handle_init_msg(line):
            match = re.search(r'Bootstrapped (\d+)%', line)
            if match:
                dashboard_state['instances'][self.country]['status'] = f"🟡 Bootstrapping {match.group(1)}%"
            logger.info(f"[{self.country}] {line}")

        try:
            self.process = stem.process.launch_tor_with_config(
                config=config,
                tor_cmd=self.tor_cmd,
                take_ownership=False,
                init_msg_handler=handle_init_msg,
                timeout=60
            )
            self.active = True
            dashboard_state['instances'][self.country]['status'] = "🟢 Online. Testing..."
            logger.info(f"[{self.country}] Tor instance successfully started and bootstrapped.")

            def drain_stdout(stream):
                try:
                    for _ in stream:
                        pass
                except:
                    pass
            if self.process and self.process.stdout:
                threading.Thread(target=drain_stdout, args=(self.process.stdout,), daemon=True).start()
        except Exception as e:
            logger.error(f"[{self.country}] Failed to start Tor: {e}")
            err_msg = str(e).split('\n')[0][:30]
            dashboard_state['instances'][self.country]['status'] = f"🔴 {err_msg}"
            self.active = False
            
    def request_new_ip(self, reason):
        logger.info(f"[{self.country}] Requesting new IP. Reason: {reason}")
        
        if self.fingerprint_index < len(self.available_fingerprints):
            best_fingerprint = self.available_fingerprints[self.fingerprint_index]
            self.fingerprint_index += 1
            dashboard_state['instances'][self.country]['status'] = f"🟡 Optimizing ({best_fingerprint[:8]})..."
            logger.info(f"[{self.country}] Auto-healing using specific high-bandwidth fingerprint: {best_fingerprint}")
            try:
                with Controller.from_port(address='127.0.0.1', port=self.control_port) as controller:
                    controller.authenticate()
                    controller.set_conf('ExitNodes', f'${best_fingerprint}')
                    controller.signal(Signal.NEWNYM)
                time.sleep(3)
            except Exception as e:
                logger.error(f"[{self.country}] Failed to SETCONF: {e}")
                err_msg = str(e).split('\n')[0][:30]
                dashboard_state['instances'][self.country]['status'] = f"🔴 Opt Fail: {err_msg}"
        else:
            dashboard_state['instances'][self.country]['status'] = "🟡 Auto-Healing (Random)..."
            try:
                with Controller.from_port(address='127.0.0.1', port=self.control_port) as controller:
                    controller.authenticate()
                    controller.set_conf('ExitNodes', f'{{{self.country}}}')
                    controller.signal(Signal.NEWNYM)
                time.sleep(3)
            except Exception as e:
                logger.error(f"[{self.country}] Failed to send NEWNYM: {e}")
                err_msg = str(e).split('\n')[0][:30]
                dashboard_state['instances'][self.country]['status'] = f"🔴 Opt Fail: {err_msg}"
            
    def stop(self):
        self.active = False
        if self.process:
            logger.info(f"[{self.country}] Stopping Tor instance...")
            try:
                self.process.kill()
                self.process.wait()
            except Exception as e:
                logger.error(f"[{self.country}] Error while stopping process: {e}")
            logger.info(f"[{self.country}] Tor instance stopped.")

def get_current_ip(proxies):
    try:
        r = requests.get("https://api.ipify.org", proxies=proxies, timeout=5)
        if r.status_code == 200:
            return r.text.strip()
    except:
        pass
    return "Unknown IP"

def measure_speed_and_ping(instance):
    proxies = {
        'http': f'socks5h://127.0.0.1:{instance.socks_port}',
        'https': f'socks5h://127.0.0.1:{instance.socks_port}'
    }
    ttfb = 0
    speed_kbs = 0
    success = False
    actual_country = None
    
    # Measure TTFB (Ping) and get GeoIP
    try:
        start_time = time.time()
        r = requests.get(TEST_URL, proxies=proxies, timeout=TIMEOUT)
        ttfb = time.time() - start_time
        if r.status_code == 200:
            success = True
            for line in r.text.split('\n'):
                if line.startswith('loc='):
                    actual_country = line.split('=')[1].lower()
                    break
    except Exception as e:
        logger.debug(f"[{instance.country}] Ping failed: {e}")
        return False, 0, 0, None
        
    # Measure Speed
    try:
        start_time = time.time()
        r = requests.get(SPEED_TEST_URL, proxies=proxies, timeout=TIMEOUT)
        if r.status_code == 200:
            elapsed = time.time() - start_time
            size_kb = len(r.content) / 1024
            speed_kbs = size_kb / elapsed
    except Exception as e:
        logger.debug(f"[{instance.country}] Speed test failed: {e}")
        pass
        
    return success, ttfb, speed_kbs, actual_country

def monitor_instance(instance):
    time.sleep(random.uniform(0.1, 15.0))
    proxies = {
        'http': f'socks5h://127.0.0.1:{instance.socks_port}',
        'https': f'socks5h://127.0.0.1:{instance.socks_port}'
    }
    
    consecutive_failures = 0
    
    while True:
        if not instance.active:
            # Check if it's currently trying to start (status contains Bootstrapping)
            status = dashboard_state['instances'][instance.country]['status']
            if "Bootstrapping" in status and "Opt Fail" not in status and "🔴" not in status:
                time.sleep(5)
                continue
                
            # If it's completely dead or failed to start, hard restart it
            logger.warning(f"[{instance.country}] Instance inactive or timed out. Hard restarting...")
            dashboard_state['instances'][instance.country]['status'] = "🔴 Dead. Restarting..."
            instance.stop()
            time.sleep(2)
            # Run start in a separate thread so it doesn't block the monitor loop
            threading.Thread(target=instance.start, daemon=True).start()
            consecutive_failures = 0
            time.sleep(15)
            continue
            
        success, ttfb, speed_kbs, actual_country = measure_speed_and_ping(instance)
        
        if success:
            consecutive_failures = 0
            if actual_country and actual_country != instance.country:
                logger.warning(f"[{instance.country}] Mismatched Country! Expected {instance.country}, got {actual_country}.")
                instance.request_new_ip(f"Wrong Country ({actual_country.upper()})")
                time.sleep(3)
                continue
                
            current_ip = get_current_ip(proxies)
            dashboard_state['instances'][instance.country]['ip'] = current_ip
            dashboard_state['instances'][instance.country]['ping'] = f"{ttfb:.2f} s"
            dashboard_state['instances'][instance.country]['speed'] = f"{speed_kbs:.1f} KB/s"
            
            logger.info(f"[{instance.country}] Proxy healthy. IP: {current_ip}, Ping: {ttfb:.2f}s, Speed: {speed_kbs:.1f} KB/s")
            
            if ttfb > LATENCY_THRESHOLD:
                instance.request_new_ip(f"High Ping ({ttfb:.2f}s)")
            elif speed_kbs < SPEED_THRESHOLD_KBS:
                instance.request_new_ip(f"Low Speed ({speed_kbs:.1f} KB/s)")
            else:
                dashboard_state['instances'][instance.country]['status'] = "🟢 Optimized"
                time.sleep(15)
        else:
            consecutive_failures += 1
            dashboard_state['instances'][instance.country]['ping'] = "Timeout"
            dashboard_state['instances'][instance.country]['speed'] = "0.0 KB/s"
            
            if consecutive_failures >= 3:
                logger.warning(f"[{instance.country}] 3 consecutive timeouts. Hard restarting...")
                dashboard_state['instances'][instance.country]['status'] = "🔴 Hard Restarting..."
                instance.stop()
                time.sleep(2)
                threading.Thread(target=instance.start, daemon=True).start()
                consecutive_failures = 0
            else:
                instance.request_new_ip("Connection Timeout")
                
            time.sleep(5)

instances = []

def cleanup_and_exit(signum, frame):
    logger.info("Termination signal received. Cleaning up...")
        
    if platform.system() == 'Windows':
        try:
            os.system('taskkill /F /IM tor.exe >nul 2>&1')
        except:
            pass
    else:
        try:
            os.system('pkill -x tor >/dev/null 2>&1')
        except:
            pass
    os._exit(0)

def get_local_geoip_paths():
    geoip_path = os.path.join(os.getcwd(), "data", "geoip").replace('\\', '/')
    geoip6_path = os.path.join(os.getcwd(), "data", "geoip6").replace('\\', '/')
    return geoip_path, geoip6_path

def discover_exit_countries(tor_cmd):
    dashboard_state['discovery_msg'] = "Starting local Tor discovery process..."
    logger.info("Starting temporary Tor process for country discovery.")
    
    geoip_path, geoip6_path = get_local_geoip_paths()
    
    discovery_data_dir = os.path.join(os.getcwd(), "tor_data", "discovery")
    if os.path.exists(discovery_data_dir):
        shutil.rmtree(discovery_data_dir, ignore_errors=True)
    os.makedirs(discovery_data_dir, exist_ok=True)
    
    config = {
        'SocksPort': '127.0.0.1:auto',
        'ControlPort': '127.0.0.1:9049',
        'CookieAuthentication': '1',
        'DataDirectory': discovery_data_dir.replace('\\', '/'),
        'ClientUseIPv6': '0',
        'ClientPreferIPv6ORPort': '0'
    }
    
    if os.path.exists(geoip_path) and os.path.exists(geoip6_path):
        logger.info(f"Local GeoIP files found. Configuring Tor to use them: {geoip_path}")
        config['GeoIPFile'] = geoip_path
        config['GeoIPv6File'] = geoip6_path
    else:
        logger.warning(f"Local GeoIP files NOT found at {geoip_path} or {geoip6_path}! Discovery will fail.")

    def handle_init_msg(line):
        match = re.search(r'Bootstrapped (\d+)%', line)
        if match:
            dashboard_state['discovery_progress'] = int(match.group(1))
            dashboard_state['discovery_msg'] = f"Bootstrapping Discovery Node: {match.group(1)}%"
        logger.info(f"[discovery] {line}")

    discovery_process = stem.process.launch_tor_with_config(
        config=config,
        tor_cmd=tor_cmd,
        take_ownership=False,
        init_msg_handler=handle_init_msg,
        timeout=300
    )
    
    country_fingerprints = {}
    country_counts = {}
    
    dashboard_state['discovery_msg'] = "Node Bootstrapped. Parsing Global Network Consensus..."
    logger.info("Tor bootstrapped. Reading network consensus...")
    
    try:
        with Controller.from_port(address='127.0.0.1', port=9049) as controller:
            controller.authenticate()
            statuses = controller.get_network_statuses()
            for desc in statuses:
                if 'Exit' in desc.flags and 'Valid' in desc.flags:
                    try:
                        country_code = controller.get_info(f"ip-to-country/{desc.address}")
                        if country_code and country_code != '??':
                            country_code = country_code.lower()
                            
                            bw = desc.bandwidth if desc.bandwidth else 0
                            if country_code not in country_fingerprints:
                                country_fingerprints[country_code] = []
                            country_fingerprints[country_code].append((desc.fingerprint, bw))
                            country_counts[country_code] = country_counts.get(country_code, 0) + 1
                    except Exception:
                        pass
                        
            # Sort fingerprints by bandwidth for each country
            for c in country_fingerprints:
                country_fingerprints[c].sort(key=lambda x: x[1], reverse=True)
                country_fingerprints[c] = [x[0] for x in country_fingerprints[c]]
                
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
    finally:
        discovery_process.kill()
        discovery_process.wait()
        # DO NOT delete the discovery_data_dir here, we need its cached files for the 15 instances!
        
    return country_counts, country_fingerprints

def generate_table():
    if dashboard_state['phase'] == 'discovery':
        table = Table(title="🔍 Tor Network Discovery Phase", box=box.ROUNDED)
        table.add_column("Status", justify="center", style="cyan")
        table.add_column("Progress", justify="center", style="magenta")
        table.add_row(dashboard_state['discovery_msg'], f"{dashboard_state['discovery_progress']}%")
        return table
    else:
        total_items = len(dashboard_state['instances'])
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        
        # Auto-rotate pages every 5 seconds
        current_page = (int(time.time() / 5) % total_pages) + 1 if total_pages > 0 else 1
        
        title = f"🌍 Tor VPN Backend - Dashboard [RAM Tier: {HARDWARE_TIER}]"
        if total_pages > 1:
            title += f" (Page {current_page}/{total_pages} - Auto Rotating)"
            
        table = Table(title=title, box=box.ROUNDED)
        table.add_column("Country", justify="center", style="cyan", no_wrap=True)
        table.add_column("SOCKS Port", justify="center", style="magenta")
        table.add_column("Current IP", justify="center", style="blue")
        table.add_column("Ping (TTFB)", justify="center", style="yellow")
        table.add_column("Speed", justify="center", style="green")
        table.add_column("Status", justify="left", style="white")

        instances_list = list(dashboard_state['instances'].items())
        start_idx = (current_page - 1) * items_per_page
        end_idx = start_idx + items_per_page

        for country, data in instances_list[start_idx:end_idx]:
            table.add_row(
                country.upper(),
                data['port'],
                data['ip'],
                data['ping'],
                data['speed'],
                data['status']
            )
        return table

def main():
    if HARDWARE_TIER == 'LOW':
        threading.stack_size(262144)
    elif HARDWARE_TIER == 'MID':
        threading.stack_size(524288)
        
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)

    base_data_dir = os.path.join(os.getcwd(), "tor_data")

    tor_cmd = "tor"
    
    if platform.system() == 'Windows':
        possible_paths = [
            os.path.join(os.getcwd(), "Tor", "tor.exe"),
            os.path.join(os.getcwd(), "tor", "tor.exe"),
            os.path.join(os.getcwd(), "tor.exe")
        ]
    else:
        possible_paths = [
            os.path.join(os.getcwd(), "tor", "tor"),
            "/usr/bin/tor",
            "/usr/local/bin/tor"
        ]
    
    for p in possible_paths:
        if os.path.exists(p):
            tor_cmd = p
            break

    # Start live dashboard loop in the main thread
    with Live(generate_table(), refresh_per_second=2) as live:
        
        # 1. Discover countries
        country_counts, country_fingerprints = discover_exit_countries(tor_cmd)
        
        if not country_counts:
            logger.error("Failed to discover any exit countries. Exiting.")
            cleanup_and_exit(None, None)
            
        sorted_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)
        top_countries = [c[0] for c in sorted_countries[:MAX_INSTANCES]]
        
        dashboard_state['phase'] = 'monitoring'

        # 2. Assign ports dynamically
        configs = []
        base_socks = 9050
        base_control = 10050
        port_mapping = {}
        
        for i, country in enumerate(top_countries):
            socks = base_socks + i
            control = base_control + i
            configs.append({"country": country, "socks_port": socks, "control_port": control})
            port_mapping[str(socks)] = country

        with open("port_mapping.json", "w") as f:
            json.dump(port_mapping, f, indent=4)

        # 3. Spawn instances
        for conf in configs:
            country = conf["country"]
            data_dir = os.path.join(base_data_dir, country)
            fingerprints = country_fingerprints.get(country, [])
            
            instance = TorInstance(
                country=country,
                socks_port=conf["socks_port"],
                control_port=conf["control_port"],
                data_dir=data_dir,
                tor_cmd=tor_cmd,
                available_fingerprints=fingerprints
            )
            instances.append(instance)
            # Start instance in a background thread to allow UI to update
            threading.Thread(target=instance.start, daemon=True).start()
            if HARDWARE_TIER == 'LOW':
                time.sleep(1.0)
            elif HARDWARE_TIER == 'MID':
                time.sleep(0.5)
            else:
                time.sleep(0.2)
            
        # Give instances a moment to start
        time.sleep(2)
        
        # 4. Start monitoring threads
        threads = []
        for instance in instances:
            t = threading.Thread(target=monitor_instance, args=(instance,), daemon=True)
            t.start()
            threads.append(t)
            
        # Keep updating table
        try:
            while True:
                live.update(generate_table())
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
            
    cleanup_and_exit(None, None)

if __name__ == "__main__":
    main()
