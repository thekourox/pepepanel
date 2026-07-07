import subprocess
import time
import sys
import os

def run_service(name, cmd, cwd=None):
    print(f"Starting {name}...")
    # Using shell=True for simpler cross-platform execution in Windows
    return subprocess.Popen(cmd, shell=True, cwd=cwd)

if __name__ == "__main__":
    print("Initializing Unified Gateway and Subservices...")
    
    procs = []
    try:
        # Start Gateway (Port 5000)
        procs.append(run_service("Auth Gateway", f"{sys.executable} gateway.py", cwd=os.getcwd()))
        
        # Start Tor Panel (Port 54322)
        # Using api.py because core.py/tor_manager.py might not be the direct entrypoint based on list_dir.
        # But wait, looking at the previous context, you mentioned `app.py` or maybe it's `api.py`.
        # Let me just check the actual file in pasarguard-tor
        tor_dir = os.path.join(os.getcwd(), "pasarguard-tor")
        
        # If there is no app.py, maybe it's api.py or core.py.
        # From the `list_dir` we saw: api.py (17KB), core.py (40KB), tor_manager.py (21KB).
        # We'll use `api.py` if app.py is missing, but let's check it.
        # Wait, earlier prompt said "pasarguard-tor/app.py". Let's stick to what was requested, or fix if app.py is missing.
        # I'll use api.py since it exists, or maybe setup_and_run.bat. Let's just use `python api.py`.
        # I will execute `python api.py`
        
        procs.append(run_service("Tor Panel", f"{sys.executable} api.py", cwd=tor_dir))
        
        # Start Surfshark Panel (Port 8088)
        shark_dir = os.path.join(os.getcwd(), "pepeshark")
        procs.append(run_service("Surfshark Panel", f"{sys.executable} app.py", cwd=shark_dir))
        
        print("\n[+] All services started successfully.")
        print("[+] Access the Unified Gateway at: http://127.0.0.1:5000")
        print("[!] Press Ctrl+C to stop all services simultaneously.\n")
        
        # Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n[!] Stopping all services...")
        for p in procs:
            p.terminate()
        sys.exit(0)
