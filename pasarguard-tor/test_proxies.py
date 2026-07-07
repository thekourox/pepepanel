import requests
import time
import json
import os
import sys
import io

# Force UTF-8 encoding for standard output to support emojis on Windows CMD
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_proxies():
    mapping_file = "port_mapping.json"
    if not os.path.exists(mapping_file):
        print("❌ Error: port_mapping.json not found! Please run the main manager script first.\n")
        return
        
    try:
        with open(mapping_file, "r") as f:
            port_mapping = json.load(f)
    except Exception as e:
        print(f"❌ Error reading port mapping file: {e}\n")
        return
        
    ports_to_test = [int(p) for p in port_mapping.keys()]
    
    print("\n==============================================================")
    print(f" 🌍 Testing {len(ports_to_test)} newly spawned proxies...")
    print("==============================================================\n")
    
    for port in ports_to_test:
        proxies = {
            'http': f'socks5h://127.0.0.1:{port}',
            'https': f'socks5h://127.0.0.1:{port}'
        }
        
        try:
            print(f"⏳ Checking Port {port} ...")
            start_time = time.time()
            # We use ipinfo.io to get IP and Country easily
            r = requests.get('https://ipinfo.io/json', proxies=proxies, timeout=15)
            elapsed_time = time.time() - start_time
            
            if r.status_code == 200:
                data = r.json()
                ip = data.get('ip', 'Unknown')
                country = data.get('country', 'Unknown')
                
                print(f"✅ Success! Connected.")
                print(f"   📍 Target Country: {port_mapping[str(port)]} | Resolved Country: {country}")
                print(f"   💻 Assigned IP: {ip}")
                print(f"   ⚡ Response Time: {elapsed_time:.2f} seconds\n")
            else:
                print(f"❌ Error! Port {port} failed to connect. (Status Code: {r.status_code})\n")
        
        except Exception as e:
             print(f"❌ Error! Port {port} failed to connect. The manager is likely auto-healing it...\n")

if __name__ == "__main__":
    test_proxies()
    print("==============================================================")
    print(" Testing completed! You may close this window.")
    print("==============================================================\n")
