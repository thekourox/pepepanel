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
import subprocess
import random
import socket
import socks
import atexit

socket.setdefaulttimeout(20)

# Setup File Logging ONLY (No console spam)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("manager_logs.txt", mode='a', encoding='utf-8')]
)
logger = logging.getLogger("TorManager")
logging.getLogger('stem').setLevel(logging.WARNING)

TEST_URL = "https://1.1.1.1/cdn-cgi/trace"
SPEED_TEST_URL = "https://speed.cloudflare.com/__down?bytes=100000" # 100KB payload
TIMEOUT = 15
LATENCY_THRESHOLD = 8.0
SPEED_THRESHOLD_KBS = 5.0
PORT_LOCK = threading.Lock()
PING_SEMAPHORE = threading.Semaphore(2)
NEXT_SOCKS_PORT = 9050
NEXT_CONTROL_PORT = 10050
G_STANDBY_POOL = []
G_COUNTRY_FINGERPRINTS = {}
G_TOR_CMD = "tor"

COUNTRY_PORTS_FILE = "country_ports.json"

def get_port_for_country(country_code):
    mapping = {}
    if os.path.exists(COUNTRY_PORTS_FILE):
        try:
            with open(COUNTRY_PORTS_FILE, "r") as f:
                mapping = json.load(f)
        except:
            pass
            
    if country_code in mapping:
        return mapping[country_code]['socks'], mapping[country_code]['control']
        
    used_socks = [v['socks'] for v in mapping.values()]
    used_control = [v['control'] for v in mapping.values()]
    
    new_socks = max(used_socks) + 1 if used_socks else 9050
    new_control = max(used_control) + 1 if used_control else 10050
    
    while new_socks in used_socks:
        new_socks += 1
    while new_control in used_control:
        new_control += 1
        
    mapping[country_code] = {'socks': new_socks, 'control': new_control}
    
    try:
        with open(COUNTRY_PORTS_FILE, "w") as f:
            json.dump(mapping, f, indent=4)
    except:
        pass
        
    return new_socks, new_control

def spawn_country(country_code):
    global instances
    
    socks_port, control_port = get_port_for_country(country_code)
    
    data_dir = os.path.join(os.getcwd(), 'tor_data', f'data_{country_code}')
    fps = G_COUNTRY_FINGERPRINTS.get(country_code, [])
    
    instance = TorInstance(country_code, socks_port, control_port, data_dir, G_TOR_CMD, fps)
    
    with QUEUE_LOCK:
        instances.append(instance)
        
    threading.Thread(target=instance.start, daemon=True).start()
    
    stagger_delay = 1
    if HARDWARE_TIER == 'ULTRA_LOW':
        stagger_delay = 5
    elif HARDWARE_TIER == 'LOW':
        stagger_delay = 2
    time.sleep(stagger_delay) # Staggered Bootstrapping delay

def detect_hardware_tier():
    try:
        ram_gb = psutil.virtual_memory().total / (1024**3)
        cores = psutil.cpu_count(logical=True) or 2
    except:
        ram_gb = 4.0
        cores = 2
        
    tier = 'HIGH'
    if ram_gb < 2.5 or cores <= 1:
        tier = 'ULTRA_LOW'
    elif ram_gb < 6.0 or cores <= 2:
        tier = 'LOW'
    elif ram_gb < 12.0 or cores <= 4:
        tier = 'MID'
        
    return tier, cores, ram_gb

HARDWARE_TIER, CPU_CORES, RAM_GB = detect_hardware_tier()

# Global variables
instances = []
dashboard_state = {
    'status': 'stopped',
    'discovery_msg': 'Waiting to start...',
    'instances': {},
    'ping_history': {} 
}

G_STANDBY_POOL = {}
QUEUE_LOCK = threading.Lock()

G_COUNTRY_FINGERPRINTS = {}
G_HOST_GUARDS = []
G_TOR_CMD = "tor"
HOST_COUNTRY = "nl"  # Default fallback

# Load settings from api.py configuration files
CONFIG_PING_INTERVAL = 10
CONFIG_RAM_LIMIT_MB = 15
CONFIG_BW_LIMIT_KB = 0

COUNTRY_PORTS_FILE = "country_ports.json"
GLOBAL_SESSION = None

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
        
        # Scheduler fields
        self.next_check_time = time.time() + 15  # Give it 15s to bootstrap initially
        self.currently_checking = False
        self.consecutive_failures = 0
        self.ping_history = []
        
        # Concurrency and Zombie protection
        self.start_lock = threading.Lock()
        
        # Init dashboard state
        dashboard_state['instances'][self.country] = {
            'port': str(self.socks_port),
            'ip_location': '...',
            'ping': '...',
            'status': '🟡 Bootstrapping...'
        }

    def start(self):
        if not self.start_lock.acquire(blocking=False):
            logger.warning(f"[{self.country}] Start already in progress. Ignoring duplicate start request.")
            return
            
        try:
            logger.info(f"[{self.country}] Starting Tor instance on SOCKS {self.socks_port}, Control {self.control_port}...")
            
            if os.path.exists(self.data_dir):
                shutil.rmtree(self.data_dir, ignore_errors=True)
            os.makedirs(self.data_dir, exist_ok=True)
            
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
            
            bind_ip = '0.0.0.0' if platform.system() == 'Linux' else '127.0.0.1'
            config = {
                'SocksPort': f'{bind_ip}:{self.socks_port}',
                'ControlPort': f'127.0.0.1:{self.control_port}',
                'CookieAuthentication': '1',
                'DataDirectory': self.data_dir.replace('\\', '/'),
                'Log': 'NOTICE stdout',
            }
            
            if HARDWARE_TIER == 'ULTRA_LOW':
                config['MaxMemInQueues'] = '8 MB'
            else:
                config['MaxMemInQueues'] = f'{CONFIG_RAM_LIMIT_MB} MB'
                
            if CONFIG_BW_LIMIT_KB > 0:
                config['BandwidthRate'] = f'{CONFIG_BW_LIMIT_KB} KBytes'
                config['BandwidthBurst'] = f'{CONFIG_BW_LIMIT_KB * 2} KBytes'
                
            # Extreme Resource Minimization for all tiers
            config['ClientOnly'] = '1'
            config['NumCPUs'] = '1'
            config['AvoidDiskWrites'] = '1'
            config['FetchDirInfoEarly'] = '0'
            config['FetchDirInfoExtraEarly'] = '0'
            config['FetchUselessDescriptors'] = '0'
            
            # Anti-Spike and Stability parameters for v2ray/PasarGuard
            config['MaxCircuitDirtiness'] = '86400'  # 24 hours (prevents sudden ping jumps)
            config['CircuitStreamTimeout'] = '300'   # 5 minutes
            config['KeepalivePeriod'] = '60'
            config['ConnectionPadding'] = '0'
            config['ReducedConnectionPadding'] = '1'
            
            if self.available_fingerprints:
                fps = [f"${fp}" for fp in self.available_fingerprints[:30]]
                config['ExitNodes'] = ",".join(fps)
                logger.info(f"[{self.country}] Assigned {len(fps)} strict fingerprints for routing.")
            else:
                config['ExitNodes'] = f'{{{self.country}}}'

            if G_HOST_GUARDS:
                g_fps = [f"${fp}" for fp in G_HOST_GUARDS]
                config['EntryNodes'] = ",".join(g_fps)
                
            # Enforce strict routing to ensure the dynamic guard logic actually applies
            config['StrictNodes'] = '1'
                
            # Relax timeout for 1-core CPUs to finish crypto handshakes without death-looping
            if HARDWARE_TIER == 'ULTRA_LOW':
                config['CircuitBuildTimeout'] = '15'
            else:
                config['CircuitBuildTimeout'] = '5'
            config['LearnCircuitBuildTimeout'] = '0'

            def handle_init_msg(line):
                match = re.search(r'Bootstrapped (\d+)%', line)
                if match:
                    dashboard_state['instances'][self.country]['status'] = f"🟡 Bootstrapping {match.group(1)}%"
                logger.info(f"[{self.country}] {line.strip()}")

            torrc_path = os.path.join(self.data_dir, "torrc")
            with open(torrc_path, "w") as f:
                for k, v in config.items():
                    f.write(f"{k} {v}\n")
                    
            self.process = subprocess.Popen(
                [self.tor_cmd, "-f", torrc_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
            )
            
            # OS-Level Process Priority De-escalation
            if self.process and self.process.pid:
                try:
                    if platform.system() == 'Windows':
                        os.system(f'wmic process where processid={self.process.pid} CALL setpriority 16384 >nul 2>&1')
                    else:
                        os.system(f'renice 10 -p {self.process.pid} >/dev/null 2>&1')
                    logger.info(f"[{self.country}] Applied OS priority de-escalation to PID {self.process.pid}")
                except Exception as e:
                    pass
            
            start_time = time.time()
            bootstrapped = False
            
            # Read stdout line by line to monitor bootstrap
            def monitor_bootstrap():
                nonlocal bootstrapped
                try:
                    for line in self.process.stdout:
                        handle_init_msg(line)
                        if "Bootstrapped 100%" in line:
                            bootstrapped = True
                            break
                        if time.time() - start_time > 45:
                            break
                except Exception:
                    pass
                    
            monitor_thread = threading.Thread(target=monitor_bootstrap, daemon=True)
            monitor_thread.start()
            monitor_thread.join(timeout=45)
            
            if not bootstrapped:
                raise Exception("Bootstrap timeout (45s) or process crashed")
                
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
            if self.process:
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                except: pass
            self.active = False
            self.process = None
        finally:
            self.start_lock.release()
            
    def request_new_ip(self, reason):
        logger.info(f"[{self.country}] Requesting new IP. Reason: {reason}")
        
        if not self.active or self.process is None:
            self.fingerprint_index = (self.fingerprint_index + 1) % max(1, len(self.available_fingerprints))
            self.stop()
            time.sleep(1)
            threading.Thread(target=self.start, daemon=True).start()
            return

        if self.available_fingerprints:
            dashboard_state['instances'][self.country]['status'] = f"🟡 Optimizing (NEWNYM)..."
            logger.info(f"[{self.country}] Auto-healing using NEWNYM on existing fingerprint pool")
            try:
                with Controller.from_port(address='127.0.0.1', port=self.control_port) as controller:
                    controller.authenticate()
                    controller.signal(Signal.NEWNYM)
                time.sleep(3)
            except Exception as e:
                logger.error(f"[{self.country}] Failed to signal NEWNYM: {e}")
                self.stop()
                time.sleep(1)
                threading.Thread(target=self.start, daemon=True).start()
        else:
            dashboard_state['instances'][self.country]['status'] = f"🟡 Optimizing (Fallback)..."
            logger.info(f"[{self.country}] Auto-healing using fallback country ExitNodes")
            try:
                with Controller.from_port(address='127.0.0.1', port=self.control_port) as controller:
                    controller.authenticate()
                    controller.set_conf('ExitNodes', f'{{{self.country}}}')
                    controller.signal(Signal.NEWNYM)
                time.sleep(3)
            except Exception as e:
                logger.error(f"[{self.country}] Failed to SETCONF: {e}")
                self.stop()
                time.sleep(1)
                threading.Thread(target=self.start, daemon=True).start()
            
    def stop(self):
        self.active = False
        
        if self.process:
            logger.info(f"[{self.country}] Stopping Tor instance...")
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception as e:
                logger.error(f"[{self.country}] Error while stopping process: {e}")
            logger.info(f"[{self.country}] Tor instance stopped.")
            self.process = None

def measure_ping(instance):
    ping_ms = 0
    actual_country = None
    success = False
    
    try:
        proxies = {
            'http': f'socks5h://127.0.0.1:{instance.socks_port}',
            'https': f'socks5h://127.0.0.1:{instance.socks_port}'
        }
        
        if not hasattr(instance, 'session'):
            import requests.adapters
            instance.session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1)
            instance.session.mount('http://', adapter)
            instance.session.mount('https://', adapter)
            
        # 1. Measure Ping using HTTP Keep-Alive
        ping_start = time.time()
        resp = instance.session.get('http://cp.cloudflare.com/generate_204', proxies=proxies, timeout=10)
        ping_end = time.time()
        
        if resp.status_code == 204:
            ping_ms = int((ping_end - ping_start) * 1000)
            success = True
            
            # 2. Check Location (reusing the same keep-alive session)
            trace_resp = instance.session.get('https://1.1.1.1/cdn-cgi/trace', proxies=proxies, timeout=10)
            if trace_resp.status_code == 200:
                for line in trace_resp.text.splitlines():
                    if line.startswith('loc='):
                        actual_country = line.split('=')[1].upper()
                        break
                        
            if not actual_country or actual_country in ['T1', 'XX', 'A1']:
                ip_resp = instance.session.get('https://ifconfig.co/country-iso', proxies=proxies, timeout=10)
                if ip_resp.status_code == 200:
                    actual_country = ip_resp.text.strip().upper()

            if not actual_country or actual_country in ['T1', 'XX', 'A1']:
                try:
                    ip_resp = instance.session.get('https://api.myip.com', proxies=proxies, timeout=10)
                    if ip_resp.status_code == 200:
                        actual_country = ip_resp.json().get('cc', '').strip().upper()
                except:
                    pass
    except Exception as e:
        success = False
            
    return success, ping_ms, actual_country

def scheduler_worker(worker_id):
    import gc
    logger.info(f"Worker {worker_id} started.")
    while dashboard_state['status'] == 'running':
        now = time.time()
        instance_to_check = None
        
        # Aggressively clean up Python memory
        if HARDWARE_TIER == 'ULTRA_LOW' and int(now) % 60 == 0:
            gc.collect()
        
        with QUEUE_LOCK:
            for inst in instances:
                # Pick up active instances, OR sleeping instances that are ready to wake up
                if not inst.currently_checking and now >= inst.next_check_time:
                    if inst.active or (not inst.active and inst.consecutive_failures >= 3):
                        instance_to_check = inst
                        inst.currently_checking = True
                        break
                    elif not inst.active and inst.consecutive_failures == 0:
                        # Failed to even start initially? Let's just retry it.
                        instance_to_check = inst
                        inst.currently_checking = True
                        break
                        
        if not instance_to_check:
            time.sleep(1)
            continue
            
        if not instance_to_check.active:
            # Waking up from sleep or retrying a dead start
            logger.info(f"[{instance_to_check.country}] Attempting to start/wake up...")
            instance_to_check.consecutive_failures = 0
            instance_to_check.next_check_time = time.time() + 30  # Give it 30s to bootstrap
            threading.Thread(target=instance_to_check.start, daemon=True).start()
            instance_to_check.currently_checking = False
            continue
        try:
            with PING_SEMAPHORE:
                success, ping_ms, actual_country = measure_ping(instance_to_check)
            
            with QUEUE_LOCK:
                if success:
                    instance_to_check.consecutive_failures = 0
                    
                    actual_country_str = actual_country if actual_country else instance_to_check.country.upper()
                    dashboard_state['instances'][instance_to_check.country]['ip_location'] = actual_country_str
                    
                    if actual_country and actual_country.lower() != instance_to_check.country.lower():
                        if actual_country in ['T1', 'XX', 'A1', 'T1', 'xx', 'a1']:
                            logger.info(f"[{instance_to_check.country}] IP location hidden by Tor network ({actual_country}), bypassing strict country check.")
                        else:
                            logger.warning(f"[{instance_to_check.country}] Mismatched Country! Expected {instance_to_check.country}, got {actual_country}.")
                            
                            instance_to_check.consecutive_failures += 1
                            if instance_to_check.consecutive_failures >= 3:
                                dashboard_state['instances'][instance_to_check.country]['status'] = f"💤 Sleeping (5m) [Bad Country: {actual_country}]"
                                instance_to_check.stop()
                                instance_to_check.consecutive_failures = 0
                                instance_to_check.next_check_time = time.time() + 300
                            else:
                                dashboard_state['instances'][instance_to_check.country]['status'] = f"🔴 Wrong Country ({actual_country})"
                                instance_to_check.request_new_ip(f"Wrong Country ({actual_country})")
                                instance_to_check.next_check_time = time.time() + 10
                                
                            instance_to_check.currently_checking = False
                            continue
                        
                    if 10000 in instance_to_check.ping_history:
                        instance_to_check.ping_history.clear()
                        
                    instance_to_check.ping_history.append(ping_ms)
                    if len(instance_to_check.ping_history) > 3:
                        instance_to_check.ping_history.pop(0)
                        
                    ema_ping = sum(instance_to_check.ping_history) / max(1, len(instance_to_check.ping_history))
                        
                    dashboard_state['instances'][instance_to_check.country]['ping'] = f"{int(ema_ping)} ms"
                    dashboard_state['instances'][instance_to_check.country]['status'] = "🟢 Online"
                    
                    if ema_ping > (LATENCY_THRESHOLD * 1000):
                        instance_to_check.request_new_ip(f"High Ping Trend ({int(ema_ping)}ms)")
                        instance_to_check.next_check_time = time.time() + 5
                    else:
                        instance_to_check.next_check_time = time.time() + CONFIG_PING_INTERVAL
                else:
                    instance_to_check.consecutive_failures += 1
                    instance_to_check.ping_history.append(10000)
                    if len(instance_to_check.ping_history) > 3:
                        instance_to_check.ping_history.pop(0)
                        
                    ema_ping = sum(instance_to_check.ping_history) / max(1, len(instance_to_check.ping_history))
                    dashboard_state['instances'][instance_to_check.country]['ping'] = "Timeout"
                    
                    if instance_to_check.consecutive_failures >= 3:
                        dashboard_state['instances'][instance_to_check.country]['status'] = f"💤 Sleeping (5m) [Network Timeout]"
                        instance_to_check.stop()
                        instance_to_check.consecutive_failures = 0
                        instance_to_check.ping_history.clear()
                        instance_to_check.next_check_time = time.time() + 300
                    else:
                        dashboard_state['instances'][instance_to_check.country]['status'] = f"🟡 Timeout. Retrying..."
                        # Do NOT request NEWNYM on timeout. It destroys the circuit before it can finish building!
                        instance_to_check.next_check_time = time.time() + 10

                    instance_to_check.next_check_time = time.time() + 10
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
        finally:
            instance_to_check.currently_checking = False

instances = []
global_thread = None

def stop_all():
    dashboard_state['status'] = 'stopped'
    dashboard_state['phase'] = 'idle'
    dashboard_state['discovery_msg'] = 'Network stopped.'
    for instance in instances:
        instance.stop()
    instances.clear()
    
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

def discover_exit_countries(tor_cmd, host_country_override=""):
    dashboard_state['phase'] = 'discovery'
    dashboard_state['discovery_msg'] = "Starting local Tor discovery process..."
    
    geoip_path = os.path.join(os.getcwd(), "data", "geoip").replace('\\\\', '/')
    geoip6_path = os.path.join(os.getcwd(), "data", "geoip6").replace('\\\\', '/')
    
    discovery_data_dir = os.path.join(os.getcwd(), "tor_data", "discovery")
    if os.path.exists(discovery_data_dir):
        shutil.rmtree(discovery_data_dir, ignore_errors=True)
    os.makedirs(discovery_data_dir, exist_ok=True)
    
    config = {
        'SocksPort': '127.0.0.1:auto',
        'ControlPort': '127.0.0.1:9049',
        'CookieAuthentication': '1',
        'DataDirectory': discovery_data_dir.replace('\\\\', '/'),
        'ClientUseIPv6': '0',
        'ClientPreferIPv6ORPort': '0'
    }
    
    if os.path.exists(geoip_path) and os.path.exists(geoip6_path):
        config['GeoIPFile'] = geoip_path
        config['GeoIPv6File'] = geoip6_path

    def handle_init_msg(line):
        match = re.search(r'Bootstrapped (\d+)%', line)
        if match:
            dashboard_state['discovery_progress'] = int(match.group(1))
            dashboard_state['discovery_msg'] = f"Bootstrapping Discovery Node: {match.group(1)}%"

    try:
        discovery_process = stem.process.launch_tor_with_config(
            config=config,
            tor_cmd=tor_cmd,
            take_ownership=False,
            init_msg_handler=handle_init_msg,
            timeout=None
        )
    except Exception as e:
        error_msg = f"Error launching Tor ({tor_cmd}): {str(e)}"
        logger.error(error_msg)
        dashboard_state['discovery_msg'] = error_msg
        return {}, {}
    
    country_fingerprints = {}
    country_counts = {}
    
    dashboard_state['discovery_msg'] = "Fetching Global Network Consensus from Onionoo API..."
    
    try:
        import urllib.request
        import json
        logger.info("Fetching global network consensus from Onionoo API...")
        url = "https://onionoo.torproject.org/details?type=relay&flag=Exit&running=true"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            for relay in data.get('relays', []):
                country_code = relay.get('country')
                if country_code:
                    country_code = country_code.lower()
                    bw = relay.get('observed_bandwidth', 0)
                    if country_code not in country_fingerprints:
                        country_fingerprints[country_code] = []
                    country_fingerprints[country_code].append((relay.get('fingerprint'), bw))
                    country_counts[country_code] = country_counts.get(country_code, 0) + 1
            
            for c in country_fingerprints:
                country_fingerprints[c].sort(key=lambda x: x[1], reverse=True)
                country_fingerprints[c] = [x[0] for x in country_fingerprints[c]]
                
        try:
            # 1. Dynamically detect the physical host server's country
            import urllib.request
            global HOST_COUNTRY
            try:
                if host_country_override:
                    HOST_COUNTRY = host_country_override.strip().lower()
                    logger.info(f"Using manual host country override for Guards: {HOST_COUNTRY.upper()}")
                else:
                    ip_req = urllib.request.Request("https://ipinfo.io/country")
                    with urllib.request.urlopen(ip_req, timeout=5) as ip_resp:
                        HOST_COUNTRY = ip_resp.read().decode().strip().lower()
                    logger.info(f"Dynamically detected host server country: {HOST_COUNTRY.upper()}")
            except Exception as e:
                logger.warning(f"Failed to detect host country, defaulting to {HOST_COUNTRY.upper()}")

            # 2. Fetch Guard nodes for that specific country and its regional neighbors
            GUARD_FALLBACKS = {
                'de': ['nl', 'fr', 'ch', 'pl', 'gb'],
                'nl': ['de', 'fr', 'gb', 'be'],
                'fr': ['de', 'nl', 'gb', 'es', 'ch'],
                'sg': ['jp', 'hk', 'kr', 'in'],
                'us': ['ca', 'mx', 'gb'],
                'gb': ['nl', 'fr', 'de', 'ie']
            }
            
            target_countries = [HOST_COUNTRY]
            if HOST_COUNTRY in GUARD_FALLBACKS:
                target_countries.extend(GUARD_FALLBACKS[HOST_COUNTRY])
            else:
                target_countries.extend(['de', 'nl', 'fr', 'gb'])
                
            guards = []
            
            # Fetch for all target countries
            for tc in target_countries:
                try:
                    req_guards = urllib.request.Request(f"https://onionoo.torproject.org/details?type=relay&flag=Guard&running=true&country={tc}")
                    with urllib.request.urlopen(req_guards, timeout=10) as resp:
                        g_data = json.loads(resp.read().decode())
                        for r in g_data.get('relays', []):
                            bw = r.get('observed_bandwidth', 0)
                            guards.append((r.get('fingerprint'), bw))
                except Exception as e:
                    logger.warning(f"Failed to fetch guards for {tc.upper()}: {e}")
                    
            guards.sort(key=lambda x: x[1], reverse=True)
            
            global G_HOST_GUARDS
            G_HOST_GUARDS = [x[0] for x in guards[:30]]
            
            if G_HOST_GUARDS:
                logger.info(f"Fetched {len(G_HOST_GUARDS)} Guard nodes from region {target_countries} for localized low-latency entry.")
            else:
                raise Exception("No guards found")
        except Exception as ge:
            logger.warning(f"Onionoo Guard fetch failed: {ge}. Using local Tor consensus (Kalak-e-Rashti)...")
            try:
                from stem.control import Controller
                local_guards = []
                with Controller.from_port(address='127.0.0.1', port=9049) as controller:
                    controller.authenticate()
                    statuses = controller.get_network_statuses()
                    for desc in statuses:
                        if 'Guard' in desc.flags and 'Valid' in desc.flags:
                            try:
                                country_code = controller.get_info(f"ip-to-country/{desc.address}")
                                if country_code and country_code.lower() == HOST_COUNTRY:
                                    bw = desc.bandwidth if desc.bandwidth else 0
                                    local_guards.append((desc.fingerprint, bw))
                            except Exception:
                                pass
                local_guards.sort(key=lambda x: x[1], reverse=True)
                G_HOST_GUARDS = [x[0] for x in local_guards[:30]]
                if G_HOST_GUARDS:
                    logger.info(f"Local Consensus: Fetched {len(G_HOST_GUARDS)} Guard nodes for {HOST_COUNTRY.upper()}.")
                else:
                    logger.warning(f"Local Consensus: No guards found for {HOST_COUNTRY.upper()}.")
            except Exception as e2:
                logger.error(f"Local Consensus Guard fallback failed: {e2}")
                
    except Exception as api_e:
        logger.warning(f"Onionoo API failed: {api_e}. Falling back to local Tor consensus...")
        dashboard_state['discovery_msg'] = "Onionoo failed. Bootstrapping local Tor for consensus..."
        
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
                            
                for c in country_fingerprints:
                    country_fingerprints[c].sort(key=lambda x: x[1], reverse=True)
                    country_fingerprints[c] = [x[0] for x in country_fingerprints[c]]
        except Exception as e:
            logger.error(f"Local Discovery failed: {e}")
            
    finally:
        try:
            discovery_process.kill()
            discovery_process.wait()
        except:
            pass
        
    return country_counts, country_fingerprints

def start_network_thread(max_instances, ping_interval, ram_limit_mb, bandwidth_limit_kb, worker_count, selected_countries, host_country_override):
    global global_thread, G_STANDBY_POOL
    global CONFIG_PING_INTERVAL, CONFIG_RAM_LIMIT_MB, CONFIG_BW_LIMIT_KB
    
    CONFIG_PING_INTERVAL = ping_interval
    CONFIG_RAM_LIMIT_MB = ram_limit_mb
    CONFIG_BW_LIMIT_KB = bandwidth_limit_kb
    
    dashboard_state['status'] = 'discovering'
    
    if HARDWARE_TIER == 'ULTRA_LOW':
        threading.stack_size(131072)
    elif HARDWARE_TIER == 'LOW':
        threading.stack_size(262144)
    elif HARDWARE_TIER == 'MID':
        threading.stack_size(524288)
        
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

    try:
        country_counts, country_fingerprints = discover_exit_countries(tor_cmd, host_country_override)
        if not country_counts:
            dashboard_state['status'] = 'stopped'
            if not dashboard_state['discovery_msg'].startswith('Error'):
                dashboard_state['discovery_msg'] = 'Discovery failed. No exit nodes found.'
            return
    except Exception as e:
        dashboard_state['status'] = 'stopped'
        dashboard_state['discovery_msg'] = f"Fatal Error: {str(e)}"
        return
        
    sorted_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)
    all_available_countries = [c[0] for c in sorted_countries]
    
    active_countries = []
    
    if selected_countries:
        preferred = [c.strip().lower() for c in selected_countries.split(",") if c.strip()]
        for p in preferred:
            if p in all_available_countries and p not in active_countries:
                active_countries.append(p)
                if len(active_countries) >= max_instances:
                    break
            elif p not in all_available_countries:
                # Add to dashboard as unsupported
                dashboard_state['instances'][p.lower()] = {
                    'socks_port': 'N/A',
                    'ip_location': 'N/A',
                    'ping': '...',
                    'status': '🔴 Unsupported (No Tor Relays)'
                }
                    
    # Then fill the rest
    for c in all_available_countries:
        if len(active_countries) >= max_instances:
            break
        if c not in active_countries:
            active_countries.append(c)
            
    global NEXT_SOCKS_PORT, NEXT_CONTROL_PORT, G_COUNTRY_FINGERPRINTS, G_TOR_CMD
    G_STANDBY_POOL = [c for c in all_available_countries if c not in active_countries]
    G_COUNTRY_FINGERPRINTS = country_fingerprints
    G_TOR_CMD = tor_cmd
    NEXT_SOCKS_PORT = 9050
    NEXT_CONTROL_PORT = 10050

    if os.path.exists("port_mapping.json"):
        try:
            os.remove("port_mapping.json")
        except:
            pass

    dashboard_state['phase'] = 'monitoring'
    dashboard_state['discovery_msg'] = f'Spawning {len(active_countries)} instances on {CPU_CORES} Cores ({RAM_GB:.1f}GB RAM)...'
    
    for country in active_countries:
        spawn_country(country)
            
    dashboard_state['discovery_msg'] = 'Monitoring instances (Scheduler Active)...'
    
    if worker_count > 0:
        actual_worker_count = worker_count
    else:
        if HARDWARE_TIER == 'ULTRA_LOW':
            actual_worker_count = 5
        elif HARDWARE_TIER == 'LOW':
            actual_worker_count = 8
        else:
            actual_worker_count = max(1, min(CPU_CORES * 2, 16))
        
    logger.info(f"Starting {actual_worker_count} scheduler workers for {len(active_countries)} instances.")
    
    dashboard_state['status'] = 'running'
    
    for i in range(actual_worker_count):
        t = threading.Thread(target=scheduler_worker, args=(i,), daemon=True)
        t.start()

def start_network(max_instances=10, ping_interval=30, ram_limit_mb=15, bandwidth_limit_kb=0, worker_count=None, selected_countries="", host_country_override=""):
    global CONFIG_PING_INTERVAL, CONFIG_RAM_LIMIT_MB, CONFIG_BW_LIMIT_KB
    
    CONFIG_PING_INTERVAL = ping_interval
    CONFIG_RAM_LIMIT_MB = ram_limit_mb
    CONFIG_BW_LIMIT_KB = bandwidth_limit_kb

    if dashboard_state['status'] == 'running':
        logger.info("Live Reload Triggered: Diffing countries...")
        preferred = [c.strip().lower() for c in selected_countries.split(",") if c.strip()]
        
        # We need all available countries sorted by count to fill the rest
        all_available = list(G_COUNTRY_FINGERPRINTS.keys())
        all_available.sort(key=lambda x: len(G_COUNTRY_FINGERPRINTS[x]), reverse=True)
        
        desired_countries = []
        for p in preferred:
            if p in all_available and p not in desired_countries:
                desired_countries.append(p)
                if len(desired_countries) >= max_instances:
                    break
                    
        for c in all_available:
            if len(desired_countries) >= max_instances:
                break
            if c not in desired_countries:
                desired_countries.append(c)
                
        with QUEUE_LOCK:
            current_countries = [inst.country for inst in instances]
            
            # Remove instances not in desired
            for inst in instances[:]:
                if inst.country not in desired_countries:
                    logger.info(f"Live Reload: Removing {inst.country}")
                    inst.stop()
                    instances.remove(inst)
                    if inst.country in dashboard_state['instances']:
                        del dashboard_state['instances'][inst.country]
                        
            # Add instances in desired that are not current
            for c in desired_countries:
                if c not in current_countries:
                    logger.info(f"Live Reload: Adding {c}")
                    spawn_country(c)
                    
        return True
        
    global global_thread
    global_thread = threading.Thread(target=start_network_thread, args=(max_instances, ping_interval, ram_limit_mb, bandwidth_limit_kb, worker_count, selected_countries, host_country_override), daemon=True)
    global_thread.start()
    return True
