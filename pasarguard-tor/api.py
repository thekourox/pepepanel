from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import core
import os
import json
import subprocess
import threading
import httpx
import uuid
import platform
import copy
import shutil



def get_emoji(country_code):
    try:
        return chr(ord(country_code[0].upper()) + 127397) + chr(ord(country_code[1].upper()) + 127397)
    except:
        return ""

COUNTRY_MAP = {
    "US": "United States", "GB": "United Kingdom", "DE": "Germany", "FR": "France", "NL": "Netherlands",
    "CA": "Canada", "SG": "Singapore", "JP": "Japan", "AU": "Australia", "IT": "Italy", "ES": "Spain",
    "CH": "Switzerland", "SE": "Sweden", "NO": "Norway", "FI": "Finland", "DK": "Denmark", "IE": "Ireland",
    "AT": "Austria", "BE": "Belgium", "PL": "Poland", "RO": "Romania", "BG": "Bulgaria", "HR": "Croatia",
    "CZ": "Czechia", "PT": "Portugal", "IS": "Iceland", "TR": "Turkey", "ID": "Indonesia", "VN": "Vietnam",
    "IN": "India", "BR": "Brazil", "ZA": "South Africa", "AE": "United Arab Emirates", "IL": "Israel",
    "HK": "Hong Kong", "TW": "Taiwan", "KR": "South Korea", "NZ": "New Zealand", "MX": "Mexico"
}

app = FastAPI(title="Tor VPN Backend Panel")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists
import os
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.on_event("startup")
async def startup_event():
    import asyncio
    import core
    # Auto-start with 60 instances
    core.start_network(
        max_instances=60,
        ping_interval=30,
        ram_limit_mb=30,
        bandwidth_limit_kb=0,
        worker_count=0,
        selected_countries="",
        host_country_override=""
    )

@app.get("/")
def get_index():
    import os
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/status")
async def get_status():
    return core.dashboard_state

CONFIG_FILE = "config.json"

def get_auto_config():
    tier = core.HARDWARE_TIER
    if tier == 'ULTRA_LOW':
        return {
            'max_instances': 40,
            'ping_interval': 60,
            'ram_limit_mb': 5,
            'bandwidth_limit_kb': 0,
            'worker_count': 5,
            'selected_countries': ""
        }
    elif tier == 'LOW':
        return {
            'max_instances': 15,
            'ping_interval': 60,
            'ram_limit_mb': 15,
            'bandwidth_limit_kb': 0,
            'worker_count': 5,
            'selected_countries': ""
        }
    elif tier == 'MID':
        return {
            'max_instances': 40,
            'ping_interval': 30,
            'ram_limit_mb': 30,
            'bandwidth_limit_kb': 0,
            'worker_count': 0,
            'selected_countries': ""
        }
    else:
        return {
            'max_instances': 100,
            'ping_interval': 15,
            'ram_limit_mb': 50,
            'bandwidth_limit_kb': 0,
            'worker_count': 16,
            'selected_countries': ""
        }

class StartConfig(BaseModel):
    max_instances: int = None
    ping_interval: int = None
    ram_limit_mb: int = None
    bandwidth_limit_kb: int = None
    worker_count: int = None
    selected_countries: str = ""
    host_country_override: str = ""

@app.post("/api/start")
async def start_network_api(config: StartConfig):
    try:
        max_c = config.max_instances if config.max_instances else get_auto_config()['max_instances']
        ping_i = config.ping_interval if config.ping_interval else get_auto_config()['ping_interval']
        ram_l = config.ram_limit_mb if config.ram_limit_mb else get_auto_config()['ram_limit_mb']
        bw_l = config.bandwidth_limit_kb if config.bandwidth_limit_kb else get_auto_config()['bandwidth_limit_kb']
        
        core.start_network(
            max_instances=max_c,
            ping_interval=ping_i,
            ram_limit_mb=ram_l,
            bandwidth_limit_kb=bw_l,
            worker_count=config.worker_count,
            selected_countries=config.selected_countries,
            host_country_override=config.host_country_override
        )
        return {"status": "success", "message": "Network starting..."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/stop")
async def stop_network():
    core.stop_all()
    return {"status": "success", "message": "Network stopped"}

class PasargardInjectRequest(BaseModel):
    pasargard_url: str
    pasargard_token: str
    template_inbound_id: str
    core_id: str = None

class LifecycleRequest(BaseModel):
    action: str
    pasargard_url: str
    pasargard_token: str

class FetchCoresRequest(BaseModel):
    pasargard_url: str
    pasargard_token: str

class FetchInboundsRequest(BaseModel):
    pasargard_url: str
    pasargard_token: str
    core_id: str

class PasargardInjectRequest(BaseModel):
    pasargard_url: str
    pasargard_token: str
    core_id: str
    template_inbound_id: str
    server_ip: str = "127.0.0.1"

@app.post("/api/pasargard/cores")
async def fetch_cores_api(req: FetchCoresRequest):
    try:
        async with httpx.AsyncClient() as client:
            auth_header = {"Authorization": f"Bearer {req.pasargard_token.replace('Bearer ', '')}"}
            host_url = req.pasargard_url.rstrip('/')
            
            resp = await client.get(f"{host_url}/api/cores/simple", headers=auth_header, timeout=10.0)
            if not resp.is_success:
                return {"status": "error", "message": f"Failed to fetch cores: {resp.text}"}
                
            cores = [{"id": str(c["id"]), "setting_key": c["name"]} for c in resp.json().get("cores", [])]
            return {"status": "success", "cores": cores}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/pasargard/inbounds")
async def fetch_inbounds_api(req: FetchInboundsRequest):
    try:
        async with httpx.AsyncClient() as client:
            auth_header = {"Authorization": f"Bearer {req.pasargard_token.replace('Bearer ', '')}"}
            host_url = req.pasargard_url.rstrip('/')
            
            resp = await client.get(f"{host_url}/api/hosts", headers=auth_header, timeout=10.0)
            if not resp.is_success:
                return {"status": "error", "message": f"Failed to fetch inbounds: {resp.text}"}
                
            inbounds = []
            for host in resp.json():
                inbounds.append({
                    "id": str(host["id"]),
                    "remark": host.get("remark", f"Host {host['id']}"),
                    "port": host.get("port", 0)
                })
            return {"status": "success", "inbounds": inbounds}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/inject_pasargard")
async def inject_pasargard(req: PasargardInjectRequest):
    try:
        mapping = {}
        if os.path.exists("port_mapping.json"):
            with open("port_mapping.json", "r") as f:
                mapping = json.load(f)
        elif os.path.exists("country_ports.json"):
            with open("country_ports.json", "r") as f:
                mapping_raw = json.load(f)
                mapping = {str(v['socks']): k for k, v in mapping_raw.items()}
        
        if not mapping:
            return {"status": "error", "message": "No active Group A instances found."}
            
        async with httpx.AsyncClient() as client:
            auth_header = {"Authorization": f"Bearer {req.pasargard_token.replace('Bearer ', '')}"}
            host_url = req.pasargard_url.rstrip('/')
            
            # 1. Fetch Core Config
            core_resp = await client.get(f"{host_url}/api/core/{req.core_id}", headers=auth_header, timeout=10.0)
            if not core_resp.is_success:
                return {"status": "error", "message": f"Failed to fetch Core {req.core_id}: {core_resp.text}"}
            
            core_data = core_resp.json()
            xray_config = core_data.get("config", {})
            
            if "inbounds" not in xray_config: xray_config["inbounds"] = []
            if "outbounds" not in xray_config: xray_config["outbounds"] = []
            if "routing" not in xray_config: xray_config["routing"] = {"rules": []}
            if "rules" not in xray_config["routing"]: xray_config["routing"]["rules"] = []
            
            # STRIP PREVIOUS INJECTIONS TO PREVENT DUPLICATES
            xray_config["inbounds"] = [i for i in xray_config.get("inbounds", []) if not (i.get("tag", "").startswith("grpA-in-"))]
            xray_config["outbounds"] = [o for o in xray_config.get("outbounds", []) if not (o.get("tag", "").startswith("grpA-out-"))]
            xray_config["routing"]["rules"] = [
                r for r in xray_config["routing"]["rules"] 
                if not (
                    (isinstance(r.get("inboundTag"), list) and any(t.startswith("grpA-in-") for t in r.get("inboundTag"))) or 
                    (isinstance(r.get("outboundTag"), str) and r.get("outboundTag", "").startswith("grpA-out-"))
                )
            ]
            
            existing_hosts = {}
            try:
                hosts_resp = await client.get(f"{host_url}/api/hosts", headers=auth_header, timeout=10.0)
                if hosts_resp.status_code == 200:
                    for old_h in hosts_resp.json():
                        tag = old_h.get("inbound_tag", "")
                        if tag.startswith("grpA-in-"):
                            # Tag format: grpA-in-country-uid
                            parts = tag.split("-")
                            if len(parts) >= 3:
                                country_part = parts[2].lower()
                                existing_hosts[country_part] = old_h
            except Exception as e:
                pass

            
            # 2. Fetch Template Host
            host_resp = await client.get(f"{host_url}/api/host/{req.template_inbound_id}", headers=auth_header, timeout=10.0)
            if not host_resp.is_success:
                return {"status": "error", "message": f"Failed to fetch Host {req.template_inbound_id}: {host_resp.text}"}
                
            template_host = host_resp.json()
            template_inbound_tag = template_host.get("inbound_tag")
            if not template_inbound_tag:
                return {"status": "error", "message": "Template host has no inbound_tag."}
                
            template_inbound = next((inb for inb in xray_config["inbounds"] if inb.get("tag") == template_inbound_tag), None)
            if not template_inbound:
                return {"status": "error", "message": "Template inbound tag not found in core config."}
                
            with open("tor_template.json", "w") as f:
                json.dump(template_inbound, f)
            
            used_ports = {inb.get("port") for inb in xray_config["inbounds"] if isinstance(inb.get("port"), int)}
            next_port = max(used_ports) + 1 if used_ports else 10000
            
            server_ip = "127.0.0.1"
            host_payloads = []
            
            for port, country in mapping.items():
                country_lower = country.lower()
                existing_host = existing_hosts.get(country_lower)
                
                if existing_host:
                    cloned_tag = existing_host.get("inbound_tag")
                    # Extract uid from existing tag if possible
                    parts = cloned_tag.split("-")
                    uid = parts[3] if len(parts) >= 4 else uuid.uuid4().hex[:6]
                    outbound_tag = f"grpA-out-{country_lower}-{uid}"
                    new_uuid = existing_host.get("uuid", str(uuid.uuid4()))
                    this_port = existing_host.get("port")
                    is_new = False
                else:
                    uid = uuid.uuid4().hex[:6]
                    outbound_tag = f"grpA-out-{country_lower}-{uid}"
                    cloned_tag = f"grpA-in-{country_lower}-{uid}"
                    new_uuid = str(uuid.uuid4())
                    this_port = next_port
                    next_port += 1
                    is_new = True

                # Clone Inbound for Core
                new_inbound = copy.deepcopy(template_inbound)
                new_inbound["tag"] = cloned_tag
                new_inbound["port"] = this_port
                
                # Auto-open firewall port
                os.system(f"ufw allow {this_port}/tcp >/dev/null 2>&1")
                
                xray_config["inbounds"].append(new_inbound)
                
                # Add SOCKS Outbound
                xray_config["outbounds"].append({
                    "tag": outbound_tag,
                    "protocol": "socks",
                    "settings": {"servers": [{"address": server_ip, "port": int(port)}]}
                })
                
                # Add Routing Rule
                xray_config["routing"]["rules"].insert(0, {
                    "type": "field",
                    "inboundTag": [cloned_tag],
                    "outboundTag": outbound_tag
                })
                
                if is_new:
                    # Prepare Host Payload for API
                    full_country_name = COUNTRY_MAP.get(country.upper(), country.upper())
                    flag = get_emoji(country.upper())
                    new_remark = f"{full_country_name} {flag}"
                    new_host_payload = copy.deepcopy(template_host)
                    new_host_payload.pop("id", None)
                    new_host_payload["uuid"] = new_uuid
                    new_host_payload.pop("created_at", None)
                    new_host_payload.pop("updated_at", None)
                    new_host_payload["remark"] = new_remark
                    new_host_payload["inbound_tag"] = cloned_tag
                    new_host_payload["port"] = this_port
                    host_payloads.append(new_host_payload)
                
                # Update Xray config to accept this specific UUID
                if "settings" in new_inbound and "clients" in new_inbound["settings"] and len(new_inbound["settings"]["clients"]) > 0:
                    client_template = copy.deepcopy(new_inbound["settings"]["clients"][0])
                    client_template["id"] = new_uuid
                    client_template["email"] = existing_host.get("email", new_uuid) if not is_new else new_host_payload.get("email", new_uuid)
                    new_inbound["settings"]["clients"] = [client_template]
                
            # 3. Save Core Config FIRST
            core_data["config"] = xray_config
            update_core_resp = await client.put(
                f"{host_url}/api/core/{req.core_id}?restart_nodes=false",
                headers=auth_header, json=core_data, timeout=10.0
            )
            if not update_core_resp.is_success:
                return {"status": "error", "message": f"Failed to save core config: {update_core_resp.text}"}
                
            # 4. Create Hosts
            for payload in host_payloads:
                c_resp = await client.post(f"{host_url}/api/host/", headers=auth_header, json=payload, timeout=10.0)
                if not c_resp.is_success:
                    print(f"Failed to create host {payload.get('remark')}: {c_resp.text}")
                    
            # Restart nodes finally
            await client.put(f"{host_url}/api/core/{req.core_id}?restart_nodes=true", headers=auth_header, json=core_data)

            return {"status": "success", "message": f"Successfully injected {len(mapping)} Group A nodes!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/lifecycle")
async def handle_lifecycle(req: LifecycleRequest):
    try:
        async with httpx.AsyncClient() as client:
            auth_header = {"Authorization": f"Bearer {req.pasargard_token.replace('Bearer ', '')}"}
            host_url = req.pasargard_url.rstrip('/')
            
            # Fetch all hosts
            list_resp = await client.get(f"{host_url}/api/hosts", headers=auth_header, timeout=10.0)
            if not list_resp.is_success:
                return {"status": "error", "message": f"Failed to fetch hosts: {list_resp.text}"}
                
            hosts = list_resp.json()
            tor_hosts = [h for h in hosts if h.get("inbound_tag", "").startswith("grpA-in-")]
            
            if req.action in ['enable', 'disable']:
                count = 0
                for h in tor_hosts:
                    h["enable"] = (req.action == 'enable')
                    r = await client.put(f"{host_url}/api/host/{h['id']}", headers=auth_header, json=h)
                    if r.is_success: count += 1
                
                template_inb = None
                if req.action == 'enable' and os.path.exists("tor_template.json"):
                    with open("tor_template.json", "r") as f:
                        template_inb = json.load(f)
                        
                # Restart all cores and modify inbounds
                cores_resp = await client.get(f"{host_url}/api/cores/simple", headers=auth_header)
                if cores_resp.is_success:
                    for core in cores_resp.json().get("cores", []):
                        c_id = core["id"]
                        cd_resp = await client.get(f"{host_url}/api/core/{c_id}", headers=auth_header)
                        if cd_resp.is_success:
                            c_data = cd_resp.json()
                            xc = c_data.get("config", {})
                            inbounds = xc.get("inbounds", [])
                            
                            for h in tor_hosts:
                                tag = h.get("inbound_tag")
                                if req.action == 'disable':
                                    inbounds = [i for i in inbounds if i.get("tag") != tag]
                                elif req.action == 'enable' and template_inb:
                                    if not any(i.get("tag") == tag for i in inbounds):
                                        new_inb = copy.deepcopy(template_inb)
                                        new_inb["tag"] = tag
                                        new_inb["port"] = h.get("port")
                                        inbounds.append(new_inb)
                                        
                            xc["inbounds"] = inbounds
                            c_data["config"] = xc
                            await client.put(f"{host_url}/api/core/{c_id}?restart_nodes=true", headers=auth_header, json=c_data)
                            
                return {"status": "success", "message": f"Successfully {req.action}d {count} Tor nodes and restarted core."}
                
            elif req.action == 'cleanup':
                count = 0
                for h in tor_hosts:
                    r = await client.delete(f"{host_url}/api/host/{h['id']}", headers=auth_header)
                    if r.is_success: count += 1
                    
                # Clean Core Config for ALL cores
                cores_resp = await client.get(f"{host_url}/api/cores/simple", headers=auth_header)
                for core in cores_resp.json().get("cores", []):
                    c_id = core["id"]
                    cd_resp = await client.get(f"{host_url}/api/core/{c_id}", headers=auth_header)
                    c_data = cd_resp.json()
                    xc = c_data.get("config", {})
                    
                    xc["inbounds"] = [i for i in xc.get("inbounds", []) if not i.get("tag", "").startswith("grpA-in-")]
                    xc["outbounds"] = [o for o in xc.get("outbounds", []) if not o.get("tag", "").startswith("grpA-out-")]
                    if "routing" in xc and "rules" in xc["routing"]:
                        xc["routing"]["rules"] = [r for r in xc["routing"]["rules"] if not r.get("outboundTag", "").startswith("grpA-out-")]
                        
                    await client.put(f"{host_url}/api/core/{c_id}?restart_nodes=true", headers=auth_header, json=c_data)
                
                return {"status": "success", "message": f"Successfully deleted {count} Group A nodes and cleaned core configs."}
                
            return {"status": "error", "message": "Unknown action."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=54322)
